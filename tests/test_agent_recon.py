from __future__ import annotations

import httpx
import pytest
import respx

from ads_api.models import Account, AccountTransaction, Ad
from scripts.agent_recon import (
    MutatingCallRefused,
    ReadOnlyAdsClient,
    analyze,
    collect,
)

BASE = "https://promoteapi.telegram.org"


def _ok(result):
    return httpx.Response(200, json={"ok": True, "result": result})


def _ad(ad_id: int, remaining: float, status: str = "active", **extra) -> dict:
    return {
        "ad_id": ad_id,
        "title": f"ad {ad_id}",
        "currency": "TON",
        "promote_url": "https://t.me/x",
        "cpm": 0.13,
        "placement": "video_banner",
        "target": {"type": "users", "countries": [{"country_code": "GB", "name": "UK"}]},
        "spent_budget": 1.0,
        "remaining_budget": remaining,
        "views": 100,
        "actions": 1,
        "action_type": "join",
        "status": status,
        "text": "hello",
        **extra,
    }


@pytest.mark.asyncio
async def test_recon_collects_with_read_only_calls(sample_account):
    async with respx.mock(base_url=BASE, assert_all_called=True) as mock:
        mock.post("/getCurrentAccount").mock(return_value=_ok(sample_account))
        ads_route = mock.post("/getAdsList").mock(
            side_effect=[
                _ok({"total_count": 2, "ads": [_ad(1, 4.0)], "next_offset": "p2"}),
                _ok({"total_count": 2, "ads": [_ad(2, 6.0, "stopped")]}),
            ]
        )
        mock.post("/getAccountTransactionsList").mock(
            return_value=_ok(
                {
                    "total_count": 2,
                    "transactions": [
                        {"peer": {"type": "external"}, "date": 1, "currency": "TON", "amount": 97.65},
                        {
                            "peer": {"type": "ad", "ad_id": 1, "ad_title": "x"},
                            "date": 2,
                            "currency": "TON",
                            "amount": -10.0,
                        },
                    ],
                }
            )
        )
        async with ReadOnlyAdsClient("tok") as client:
            account, ads, txs = await collect(client)
        assert ads_route.calls[0].request.content and b'"return_target":true' in ads_route.calls[0].request.content
        called = {call.request.url.path for call in mock.calls}
        assert called == {"/getCurrentAccount", "/getAdsList", "/getAccountTransactionsList"}
    report = analyze(account, ads, txs)
    assert report["sum_ad_remaining_budget"] == 10.0
    assert report["sum_live_ad_remaining_budget"] == 4.0
    assert report["ads_budget_matches_sum_ad_remaining"] is True
    assert report["verdict"].startswith("H1")
    assert report["placement_x_target_type"] == {"video_banner x users": 2}
    assert "tok" not in str(report)


@pytest.mark.asyncio
async def test_recon_client_refuses_mutations():
    async with respx.mock(base_url=BASE) as mock:
        async with ReadOnlyAdsClient("tok") as client:
            for call in (
                client.create_ad(title="t", promote_url="https://t.me/x", cpm=1, target={"type": "search", "search_queries": ["a"]}),
                client.submit_ad_for_review(ad_id=1),
                client.delete_ad(ad_id=1),
                client.increase_ad_budget(ad_id=1, amount=1),
                client.increase_account_budget(account_id="a", amount=1),
                client.edit_ad(ad_id=1, is_paused=True),
            ):
                with pytest.raises(MutatingCallRefused):
                    await call
        assert mock.calls.call_count == 0


def test_analyze_h2_verdict():
    account = Account(account_id="a", title="t", currency="TON", spent_budget=2, remaining_budget=98, ads_budget=10)
    ads = [Ad.model_validate(_ad(1, 10.0))]
    txs = [AccountTransaction.model_validate({"peer": {"type": "external"}, "date": 1, "currency": "TON", "amount": 88})]
    assert analyze(account, ads, txs)["verdict"].startswith("H2")
