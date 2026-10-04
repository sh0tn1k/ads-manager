from __future__ import annotations

import json

import pytest
import respx

from ads_api.client import AdsClient
from ads_api.models import Ad
from agent.planner.balance import FreeBalanceProvider, compute_free_balance
from agent.store import Database, Store
from agent.store.db import migration_files
from agent.sync import SyncService
from tests.agent_helpers import BASE, account, api_ad, make_config, ok

NOW = 1_790_100_000


def test_config_defaults_follow_plan_and_leave_owner_inputs_unset():
    cfg = make_config()
    assert cfg.dry_run is True
    assert cfg.db_path == ":memory:"
    assert cfg.max_pending_proposals == 3
    assert cfg.proposal_ttl_hours == 12
    assert cfg.tau_min == 0.10 and cfg.mc_samples == 4000 and cfg.cold_start_n0 == 12
    assert (cfg.stage1_budget, cfg.stage2_budget, cfg.stage3_budget) == (0.7, 3.0, 8.0)
    assert cfg.hurdle_cpa_ton is None
    assert cfg.first_live_max_budget is None
    assert cfg.llm_daily_usd_cap is None
    assert set(cfg.missing_planner_inputs()) == {
        "HURDLE_CPA_TON",
        "EXPLORE_SHARE",
        "MAX_LOSS_PER_HYPOTHESIS",
        "MAX_ACTIVE_EXPERIMENTS",
    }
    assert cfg.allowed_user_ids == frozenset({200})


def test_config_reads_env(monkeypatch):
    from agent.config import AgentConfig

    monkeypatch.setenv("ADS_DRY_RUN", "0")
    monkeypatch.setenv("HURDLE_CPA_TON", "0.5")
    monkeypatch.setenv("MAX_PENDING_PROPOSALS", "5")
    monkeypatch.setenv("FIRST_LIVE_MAX_BUDGET", "")
    cfg = AgentConfig(_env_file=None)
    assert cfg.dry_run is False
    assert cfg.hurdle_cpa_ton == 0.5
    assert cfg.max_pending_proposals == 5
    assert cfg.first_live_max_budget is None
    assert "ANTHROPIC" not in repr(cfg)


def test_migrations_are_versioned_and_idempotent(tmp_path):
    path = tmp_path / "agent.sqlite"
    db = Database(path)
    latest = max(v for v, _ in migration_files())
    assert db.schema_version == latest
    db.close()
    db2 = Database(path)
    assert db2.schema_version == latest
    tables = {r["name"] for r in db2.fetchall("SELECT name FROM sqlite_master WHERE type='table'")}
    for name in (
        "account_snapshots",
        "ads",
        "ad_snapshots",
        "ad_stats",
        "ad_factors",
        "hypotheses",
        "proposals",
        "proposal_events",
        "external_placements",
        "llm_calls",
    ):
        assert name in tables
    db2.close()


def _mock_api(mock, ads_pages, stats=None, acct=None):
    mock.post("/getCurrentAccount").mock(return_value=ok(acct or account()))
    mock.post("/getAdsList").mock(side_effect=[ok(p) for p in ads_pages])
    return mock.post("/getAdStats").mock(
        return_value=ok(
            stats
            if stats is not None
            else [
                {"from_time": 1_790_000_000, "to_time": 1_790_086_400, "views": 500, "clicks": 6, "actions": 1, "currency": "TON", "spent_budget": 0.3},
                {"from_time": 1_790_086_400, "to_time": 1_790_172_800, "views": 300, "clicks": 4, "joins": 0, "currency": "TON", "spent_budget": 0.2},
            ]
        )
    )


def _pages():
    return [
        {"total_count": 2, "ads": [api_ad(1)], "next_offset": "next"},
        {"total_count": 2, "ads": [api_ad(2, status="stopped", remaining=0.0, decline_reason=None)]},
    ]


@pytest.mark.asyncio
async def test_full_sync_reproduces_ads_list_and_stats_and_is_idempotent():
    store = Store.open(":memory:")
    async with respx.mock(base_url=BASE) as mock:
        _mock_api(mock, _pages() + _pages())
        async with AdsClient("tok") as client:
            sync = SyncService(client, store, clock=lambda: NOW)
            first = await sync.full()
            ads_req = [c.request for c in mock.calls if c.request.url.path == "/getAdsList"]
            assert all(json.loads(r.content)["return_target"] is True for r in ads_req)
            state1 = (store.ads.rows(), store.ad_stats.count(), store.ad_snapshots.count(), store.accounts.count())
            second = await sync.full()
            state2 = (store.ads.rows(), store.ad_stats.count(), store.ad_snapshots.count(), store.accounts.count())
    assert first.ads_seen == 2 and first.ad_snapshots_added == 2 and first.account_snapshot_added
    assert second.ad_snapshots_added == 0 and not second.account_snapshot_added
    assert state1 == state2
    expected = [Ad.model_validate(api_ad(1)), Ad.model_validate(api_ad(2, status="stopped", remaining=0.0))]
    assert [a.model_dump() for a in store.ads.all()] == [a.model_dump() for a in expected]
    row = store.ads.row(1)
    assert row["target_type"] == "users" and json.loads(row["target_json"])["countries"][0]["country_code"] == "GB"
    stats = store.ad_stats.for_ad(1, 86400)
    assert [(s["from_time"], s["views"], s["actions"]) for s in stats] == [
        (1_790_000_000, 500, 1),
        (1_790_086_400, 300, 0),
    ]
    assert store.ad_stats.totals(1, 86400)["spent_budget"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_snapshot_added_only_on_change():
    store = Store.open(":memory:")
    changed = [{"total_count": 1, "ads": [api_ad(1, status="on_hold", is_paused=True)]}]
    async with respx.mock(base_url=BASE) as mock:
        _mock_api(mock, [{"total_count": 1, "ads": [api_ad(1)]}] + changed, stats=[])
        async with AdsClient("tok") as client:
            sync = SyncService(client, store, clock=lambda: NOW)
            await sync.full()
            await sync.full()
    snaps = store.ad_snapshots.for_ad(1)
    assert [s["status"] for s in snaps] == ["active", "on_hold"]
    assert store.ads.row(1)["status"] == "on_hold"


@pytest.mark.asyncio
async def test_stats_sync_chunks_by_1000_intervals():
    store = Store.open(":memory:")
    async with respx.mock(base_url=BASE) as mock:
        route = mock.post("/getAdStats").mock(return_value=ok([]))
        async with AdsClient("tok") as client:
            sync = SyncService(client, store, clock=lambda: NOW)
            await sync.stats.run_for_ad(5, interval=300, from_time=NOW - 2500 * 300, to_time=NOW)
    bodies = [json.loads(c.request.content) for c in route.calls]
    assert len(bodies) == 3
    assert all(b["to_time"] - b["from_time"] <= 1000 * 300 for b in bodies)
    assert bodies[0]["from_time"] == NOW - 2500 * 300 and bodies[-1]["to_time"] == NOW


@pytest.mark.asyncio
async def test_sync_makes_only_read_calls():
    store = Store.open(":memory:")
    async with respx.mock(base_url=BASE) as mock:
        _mock_api(mock, _pages())
        async with AdsClient("tok") as client:
            await SyncService(client, store, clock=lambda: NOW).full()
    assert {c.request.url.path for c in mock.calls} <= {"/getCurrentAccount", "/getAdsList", "/getAdStats"}


def test_free_balance_formula_both_semantics():
    assert compute_free_balance(account_remaining=50, live_ads_remaining=5, reserved=2, semantics="includes_ads") == 43
    assert compute_free_balance(account_remaining=50, live_ads_remaining=5, reserved=2, semantics="excludes_ads") == 48


@pytest.mark.asyncio
async def test_free_balance_provider_counts_live_ads_and_reserves():
    store = Store.open(":memory:")
    cfg = make_config()
    store.db.execute(
        "INSERT INTO proposals(proposal_id, created_ts, expires_ts, status, kind, payload_json, payload_hash, reserved_amount)"
        " VALUES('p1', 0, 1, 'proposed', 'create_ad', '{}', 'h', 1.5), ('p2', 0, 1, 'rejected', 'create_ad', '{}', 'h', 9)"
    )
    async with respx.mock(base_url=BASE) as mock:
        _mock_api(mock, _pages(), stats=[])
        async with AdsClient("tok") as client:
            await SyncService(client, store, clock=lambda: NOW).full()
            view = await FreeBalanceProvider(client, store, cfg).current()
    assert view.live_ads_remaining == 1.0  # ad 2 is stopped
    assert view.reserved == 1.5
    assert view.free == pytest.approx(50 - 1.0 - 1.5)
