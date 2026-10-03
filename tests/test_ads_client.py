from __future__ import annotations

import httpx
import pytest
import respx

from ads_api.catalog import ADS_API_METHOD_NAMES
from ads_api.client import AdsClient
from ads_api.errors import AdsApiError, AdsAuthError, AdsRateLimitError
from ads_api.models import InputAdTargetChannels, InputAdTargetSearch, UploadFile


BASE = "https://promoteapi.telegram.org"


def _ok(result):
    return httpx.Response(200, json={"ok": True, "result": result})


@pytest.mark.asyncio
async def test_auth_header_and_json_body(sample_account):
    async with respx.mock(base_url=BASE) as mock:
        route = mock.post("/getCurrentAccount").mock(return_value=_ok(sample_account))
        async with AdsClient("secret-token") as client:
            account = await client.get_current_account()
        assert account.account_id == "acc_1"
        assert account.currency == "TON"
        request = route.calls.last.request
        assert request.headers["Authorization"] == "Bearer secret-token"
        assert request.headers["Accept"] == "application/json"


@pytest.mark.asyncio
async def test_create_ad_request_building():
    payload = {
        "ad_id": 1,
        "title": "A",
        "currency": "EUR",
        "promote_url": "https://t.me/x",
        "cpm": 2.0,
        "placement": "search_result",
        "spent_budget": 0,
        "remaining_budget": 0,
        "views": 0,
        "actions": 0,
        "status": "ready_for_review",
    }
    async with respx.mock(base_url=BASE) as mock:
        route = mock.post("/createAd").mock(return_value=_ok(payload))
        async with AdsClient("t", account_id="acc_default") as client:
            ad = await client.create_ad(
                title="A",
                promote_url="https://t.me/x",
                cpm=2,
                placement="search_result",
                target=InputAdTargetSearch(search_queries=["vpn", "proxy"]),
                idempotency_key="idem-1",
            )
        assert ad.ad_id == 1
        request = route.calls.last.request
        assert request.headers["Idempotency-Key"] == "idem-1"
        body = request.read()
        import json

        data = json.loads(body)
        assert data["account_id"] == "acc_default"
        assert data["target"]["type"] == "search"
        assert data["target"]["search_queries"] == ["vpn", "proxy"]
        assert data["placement"] == "search_result"
        assert "idempotency_key" not in data


@pytest.mark.asyncio
async def test_create_ad_channels_target():
    payload = {
        "ad_id": 2,
        "title": "Ch",
        "currency": "EUR",
        "promote_url": "https://t.me/x",
        "cpm": 1,
        "placement": "channel_post",
        "spent_budget": 0,
        "remaining_budget": 0,
        "views": 0,
        "actions": 0,
        "status": "in_review",
        "target": {"type": "channels", "language_codes": ["en"]},
    }
    # returned target uses languages objects in real API; client still parses if type matches
    payload["target"] = {"type": "channels", "languages": [{"language_code": "en", "name": "English"}]}
    async with respx.mock(base_url=BASE) as mock:
        mock.post("/createAd").mock(return_value=_ok(payload))
        async with AdsClient("t") as client:
            ad = await client.create_ad(
                title="Ch",
                promote_url="https://t.me/x",
                cpm=1,
                placement="channel_post",
                target=InputAdTargetChannels(language_codes=["en"]),
            )
        assert ad.target is not None
        assert ad.target.type == "channels"


@pytest.mark.asyncio
async def test_upload_multipart():
    async with respx.mock(base_url=BASE) as mock:
        route = mock.post("/uploadAdPhoto").mock(
            return_value=_ok({"photo_id": "ph1", "photo_url": "https://cdn/x.jpg"})
        )
        async with AdsClient("t") as client:
            photo = await client.upload_ad_photo(
                UploadFile(b"jpeg-bytes", filename="ad.jpg"),
                account_id="acc",
            )
        assert photo.photo_id == "ph1"
        request = route.calls.last.request
        content_type = request.headers["Content-Type"]
        assert content_type.startswith("multipart/form-data")
        body = request.content
        assert b"ad.jpg" in body
        assert b"jpeg-bytes" in body


@pytest.mark.asyncio
async def test_error_and_auth():
    async with respx.mock(base_url=BASE) as mock:
        mock.post("/deleteAd").mock(return_value=httpx.Response(200, json={"ok": False, "error": "NOT_ENOUGH_BUDGET"}))
        async with AdsClient("t") as client:
            with pytest.raises(AdsApiError) as ei:
                await client.delete_ad(ad_id=1)
            assert ei.value.error == "NOT_ENOUGH_BUDGET"

        mock.post("/getAccountInfo").mock(return_value=httpx.Response(401, json={"ok": False, "error": "UNAUTHORIZED"}))
        async with AdsClient("t") as client:
            with pytest.raises(AdsAuthError):
                await client.get_account_info()


@pytest.mark.asyncio
async def test_rate_limit_retries_then_raises():
    async with respx.mock(base_url=BASE) as mock:
        mock.post("/getAdsList").mock(return_value=httpx.Response(429, headers={"Retry-After": "0"}))
        async with AdsClient("t", max_retries=2) as client:
            with pytest.raises(AdsRateLimitError):
                await client.get_ads_list()


@pytest.mark.asyncio
async def test_get_ads_list_and_iter(sample_ad):
    page1 = {"total_count": 2, "ads": [sample_ad], "next_offset": "n2"}
    page2 = {
        "total_count": 2,
        "ads": [{**sample_ad, "ad_id": 43}],
        "next_offset": None,
    }
    async with respx.mock(base_url=BASE) as mock:
        mock.post("/getAdsList").mock(side_effect=[_ok(page1), _ok(page2)])
        async with AdsClient("t") as client:
            collected = [ad.ad_id async for ad in client.iter_ads(limit=1)]
        assert collected == [42, 43]


@pytest.mark.asyncio
async def test_account_transactions_accept_int_next_offset():
    payload = {
        "total_count": 40,
        "transactions": [
            {
                "peer": {"type": "external", "comment": "topup"},
                "date": 1,
                "currency": "TON",
                "amount": 10,
            }
        ],
        "next_offset": 30,
    }
    async with respx.mock(base_url=BASE) as mock:
        mock.post("/getAccountTransactionsList").mock(return_value=_ok(payload))
        async with AdsClient("t") as client:
            listing = await client.get_account_transactions_list(limit=30)
        assert listing.next_offset == 30
        assert listing.transactions[0].peer.type == "external"


@pytest.mark.asyncio
async def test_stats_and_audiences_and_pixel_and_targeting():
    async with respx.mock(base_url=BASE) as mock:
        mock.post("/getAccountStats").mock(
            return_value=_ok(
                [
                    {
                        "from_time": 1,
                        "to_time": 2,
                        "views": 3,
                        "clicks": 1,
                        "joins": 9,
                        "currency": "EUR",
                        "spent_budget": 0.2,
                    }
                ]
            )
        )
        mock.post("/getAudiencesList").mock(
            return_value=_ok(
                {
                    "total_count": 1,
                    "audiences": [
                        {
                            "audience_id": 7,
                            "title": "VIP",
                            "size": 50,
                            "ads_count": 0,
                            "created_date": 1,
                            "updated_date": 2,
                        }
                    ],
                }
            )
        )
        mock.post("/getPixel").mock(return_value=_ok({"pixel_id": "px", "code_snippet": "<script>"}))
        mock.post("/getTargetLanguagesList").mock(
            return_value=_ok([{"language_code": "ru", "name": "Russian"}])
        )
        mock.post("/getTargetBot").mock(
            return_value=_ok({"bot_id": 1, "title": "Bot", "username": "xbot"})
        )
        async with AdsClient("t") as client:
            stats = await client.get_account_stats(from_time=1, to_time=2, interval=300)
            assert stats[0].actions == 9
            audiences = await client.get_audiences_list()
            assert audiences.audiences[0].title == "VIP"
            pixel = await client.get_pixel()
            assert pixel.pixel_id == "px"
            langs = await client.get_target_languages_list()
            assert langs[0].language_code == "ru"
            bot = await client.get_target_bot("@xbot")
            assert bot.username == "xbot"


@pytest.mark.asyncio
async def test_billing_and_report_and_locations(sample_account):
    related = dict(sample_account, account_id="acc_2", title="Child")
    tx = {
        "transaction_id": "tx1",
        "status": "completed",
        "currency": "TON",
        "amount": 3.0,
        "main_account": sample_account,
        "related_account": related,
    }
    async with respx.mock(base_url=BASE) as mock:
        mock.post("/increaseAccountBudget").mock(return_value=_ok(tx))
        mock.post("/getAccountReport").mock(
            return_value=_ok(
                [
                    {
                        "ad_id": 1,
                        "ad_title": "A",
                        "views": 10,
                        "clicks": 1,
                        "actions": 0,
                        "currency": "TON",
                        "spent_budget": 0.4,
                    }
                ]
            )
        )
        mock.post("/getTargetLocationsList").mock(
            return_value=_ok(
                {
                    "total_count": 1,
                    "locations": [{"location_id": 9, "name": "Moscow", "country_code": "RU"}],
                    "next_offset": "",
                }
            )
        )
        mock.post("/submitAdForReview").mock(
            return_value=_ok(
                {
                    "ad_id": 42,
                    "title": "T",
                    "currency": "TON",
                    "promote_url": "https://t.me/x",
                    "cpm": 1,
                    "spent_budget": 0,
                    "remaining_budget": 1,
                    "views": 0,
                    "actions": 0,
                    "status": "in_review",
                }
            )
        )
        async with AdsClient("t") as client:
            status = await client.increase_account_budget(account_id="acc_2", amount=3)
            assert status.status == "completed"
            report = await client.get_account_report(year=2026, month=9)
            assert report[0].ad_id == 1
            locs = await client.get_target_locations_list(country_code="RU", query="Mos")
            assert locs.locations[0].name == "Moscow"
            ad = await client.submit_ad_for_review(ad_id=42)
            assert ad.status == "in_review"


@pytest.mark.asyncio
async def test_camelcase_aliases_cover_catalog():
    missing = []
    for name in ADS_API_METHOD_NAMES:
        if not hasattr(AdsClient, name):
            missing.append(name)
    assert missing == []


@pytest.mark.asyncio
async def test_deprecated_edit_ad_status_still_posts():
    async with respx.mock(base_url=BASE) as mock:
        route = mock.post("/editAdStatus").mock(return_value=_ok(True))
        async with AdsClient("t") as client:
            result = await client.edit_ad_status(ad_id=1, is_paused=True)
        assert result is True
        assert route.called
