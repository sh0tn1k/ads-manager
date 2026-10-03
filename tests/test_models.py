from __future__ import annotations

from ads_api.models import (
    AccountTransaction,
    AccountTransactionsList,
    Ad,
    AdTargetUsers,
    InputAdTargetUsers,
    TransactionStatus,
    dump_param,
)


def test_account_transactions_list_int_offset():
    listing = AccountTransactionsList.model_validate(
        {
            "total_count": 40,
            "transactions": [
                {
                    "peer": {"type": "ad", "ad_id": 1, "ad_title": "A"},
                    "date": 1,
                    "currency": "TON",
                    "amount": -0.5,
                }
            ],
            "next_offset": 30,
        }
    )
    assert listing.next_offset == 30
    empty = AccountTransactionsList.model_validate(
        {"total_count": 0, "transactions": [], "next_offset": ""}
    )
    assert empty.next_offset is None


def test_joins_alias_on_ad():
    ad = Ad.model_validate(
        {
            "ad_id": 1,
            "title": "t",
            "currency": "XTR",
            "promote_url": "https://t.me/x",
            "cpm": 1,
            "spent_budget": 0,
            "remaining_budget": 0,
            "views": 0,
            "joins": 5,
            "status": "active",
        }
    )
    assert ad.actions == 5


def test_target_users_dump():
    target = InputAdTargetUsers(country_codes=["RU"], device="ios")
    dumped = dump_param(target)
    assert dumped["type"] == "users"
    assert dumped["country_codes"] == ["RU"]
    assert dumped["device"] == "ios"


def test_account_transaction_peer_discriminator():
    tx = AccountTransaction.model_validate(
        {
            "peer": {"type": "ad", "ad_id": 9, "ad_title": "Hello"},
            "date": 1,
            "currency": "EUR",
            "amount": -1.5,
        }
    )
    assert tx.peer.type == "ad"
    assert tx.peer.ad_id == 9


def test_transaction_status_without_main_account():
    item = TransactionStatus.model_validate(
        {
            "transaction_id": "tx1",
            "status": "completed",
            "currency": "TON",
            "amount": 1,
        }
    )
    assert item.main_account is None
    assert item.related_account is None


def test_ad_target_users_from_api():
    target = AdTargetUsers.model_validate(
        {
            "type": "users",
            "countries": [{"country_code": "RU", "name": "Russia"}],
            "device": "android",
        }
    )
    assert target.countries[0].country_code == "RU"
