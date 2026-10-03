from __future__ import annotations

import pytest

from ads_api.client import AdsClient
from ads_api.models import Account


@pytest.fixture
def ads_token() -> str:
    return "test-ads-token"


@pytest.fixture
def sample_account() -> dict:
    return {
        "account_id": "acc_1",
        "title": "Main",
        "currency": "TON",
        "spent_budget": 1.5,
        "remaining_budget": 87.65,
        "ads_budget": 10.0,
    }


@pytest.fixture
def sample_ad() -> dict:
    return {
        "ad_id": 42,
        "title": "Test ad",
        "currency": "TON",
        "text": "Hello",
        "promote_url": "https://t.me/durov",
        "cpm": 1.2,
        "placement": "channel_post",
        "spent_budget": 0.1,
        "remaining_budget": 5,
        "views": 10,
        "clicks": 2,
        "actions": 1,
        "status": "active",
        "created_date": 1700000000,
    }
