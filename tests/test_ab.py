from __future__ import annotations

from pathlib import Path

import pytest

from bot.ab import (
    AbStore,
    compare_counts,
    compare_payloads,
    creative_changes,
    format_delta,
    signed_delta,
    verdict,
)


def test_signed_delta_positive_means_b_better():
    assert signed_delta(0.01, 0.02, lower_is_better=False) == 100
    assert signed_delta(2.0, 1.0, lower_is_better=True) == 50
    assert signed_delta(2.0, 3.0, lower_is_better=True) == pytest.approx(-50)
    assert signed_delta(0, 1, lower_is_better=False) is None
    assert format_delta(12.34) == "+12.3%"
    assert format_delta(-8) == "-8.0%"


def test_overall_positive_when_new_variant_is_more_efficient():
    a = {"views": 1000, "clicks": 10, "actions": 2, "spent": 10}
    b = {"views": 500, "clicks": 10, "actions": 3, "spent": 4}
    result = compare_counts(a, b)
    assert result.a.ctr == pytest.approx(0.01)
    assert result.b.ctr == pytest.approx(0.02)
    assert result.b.cpc == pytest.approx(0.4)
    assert result.b.cpa == pytest.approx(4 / 3)
    assert result.overall is not None and result.overall > 0
    text = verdict(result.overall, b_views=result.b.views)
    assert "B" in text
    assert text.startswith("Общий процент: +")


def test_overall_negative_when_original_is_better():
    a = {"views": 1000, "clicks": 20, "actions": 4, "spent": 4}
    b = {"views": 1000, "clicks": 5, "actions": 1, "spent": 8}
    result = compare_counts(a, b)
    assert result.overall is not None and result.overall < 0
    text = verdict(result.overall, b_views=result.b.views)
    assert "A" in text
    assert "Общий процент: -" in text or "Общий процент: −" in text


def test_incremental_compare_uses_only_new_delivery():
    baseline = {
        "ad": {
            "views": 1000,
            "clicks": 10,
            "actions": 2,
            "spent_budget": 10,
            "title": "Old",
            "text": "Hi",
        }
    }
    current = {
        "ad": {
            "views": 1500,
            "clicks": 20,
            "actions": 5,
            "spent_budget": 14,
            "title": "Old",
            "text": "Hi",
            "photo": {"photo_id": "ph1"},
        }
    }
    result = compare_payloads(baseline, current)
    assert result.b.views == 500
    assert result.b.clicks == 10
    assert result.b.actions == 3
    assert result.b.spent == 4
    assert "добавили фото" in creative_changes(baseline, current)
    assert "ещё нет" in verdict(None, b_views=0)


@pytest.mark.asyncio
async def test_ab_store_one_running_per_ad():
    path = Path("data") / "test_ab_store.sqlite"
    path.parent.mkdir(exist_ok=True)
    for leftover in path.parent.glob("test_ab_store.sqlite*"):
        leftover.unlink(missing_ok=True)
    store = AbStore(path)
    first = await store.start(
        user_id=1,
        chat_id=10,
        account_id="acc",
        ad_id=156,
        note="добавил картинку",
        baseline={"ad": {"views": 1000, "clicks": 5, "actions": 1, "spent_budget": 2}},
    )
    same = await store.start(
        user_id=1,
        chat_id=10,
        account_id="acc",
        ad_id=156,
        note="ignored",
        baseline={"ad": {"views": 0}},
    )
    assert first.id == same.id
    assert first.note == "добавил картинку"
    finished = await store.finish(
        first.id,
        1,
        {"ad": {"views": 1300, "clicks": 8, "actions": 2, "spent_budget": 3}},
    )
    assert finished is not None
    assert finished.status == "done"
    assert await store.get_running(1, 156) is None
    second = await store.start(
        user_id=1,
        chat_id=10,
        account_id="acc",
        ad_id=156,
        note="текст",
        baseline={"ad": {"views": 1300}},
    )
    assert second.id != first.id
    assert await store.get(first.id, 2) is None
    await store.delete(second.id, 1)
    ids = [item.id for item in await store.list_for_user(1)]
    assert second.id not in ids
    assert first.id in ids
