from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest
import respx
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ads_api.client import AdsClient
from agent.proposals.models import AdSpec, Cost, ProposalKind, ProposalPayload, ProposalStatus
from agent.services import AgentServices
from agent.store import Store
from tests.agent_helpers import BASE, PROMOTE_URL, Clock, FakeAds, account, api_ad, build_services, make_config, ok

USER = 200


def payload(budget: float = 0.5, **ad_over) -> ProposalPayload:
    ad = dict(
        title="T",
        text="Premier League daily",
        promote_url=PROMOTE_URL,
        cpm=0.13,
        placement="video_banner",
        target={"type": "users", "country_codes": ["GB"]},
        initial_budget=budget,
    )
    ad.update(ad_over)
    return ProposalPayload(
        kind=ProposalKind.CREATE_AD, ad=AdSpec(**ad), cost=Cost(initial_budget=budget, max_loss=budget, reserved=budget)
    )


async def _create(services, budget=0.5, **over):
    result = await services.proposals.create(payload(budget, **over))
    assert result.proposal is not None, result.errors
    return result.proposal


# --- end-to-end with the real AdsClient over respx ---


@pytest.mark.asyncio
async def test_e2e_accept_creates_ad_in_dry_run_without_mutating_calls():
    async with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        mock.post("/getCurrentAccount").mock(return_value=ok(account(remaining=10)))
        create = mock.post("/createAd").mock(return_value=ok(api_ad(1)))
        async with AdsClient("tok") as client:
            services = AgentServices.build(client, make_config(), store=Store.open(":memory:"), validate_dictionaries=False)
            results = await services.proposals.inject_test_proposals(USER)
            assert all(r.proposal for r in results), [r.errors for r in results]
            pid = results[0].proposal.proposal_id
            decision = await services.proposals.accept(pid, USER)
        assert create.call_count == 0
        assert {c.request.url.path for c in mock.calls} == {"/getCurrentAccount"}
    assert decision.code == "dry_run"
    proposal = services.store.proposals.get(pid)
    assert proposal.status == ProposalStatus.CREATED and proposal.dry_run
    events = services.store.proposals.events(pid)
    logged = [e for e in events if e["event"] == "dry_run_create_ad"][0]["details"]["request"]
    assert logged["idempotency_key"] == pid
    assert logged["return_target"] is True and logged["promote_url"] == PROMOTE_URL
    assert "account_id" not in logged


@pytest.mark.asyncio
async def test_e2e_live_mode_sends_idempotency_key_and_submits_ready_for_review():
    async with respx.mock(base_url=BASE) as mock:
        mock.post("/getCurrentAccount").mock(return_value=ok(account(remaining=10)))
        create = mock.post("/createAd").mock(return_value=ok(api_ad(77, status="ready_for_review", remaining=0.1)))
        submit = mock.post("/submitAdForReview").mock(
            side_effect=[httpx.Response(500, json={"ok": False, "error": "INTERNAL"}), ok(api_ad(77, status="in_review"))]
        )
        async with AdsClient("tok", max_retries=1) as client:
            cfg = make_config(dry_run=False, first_live_max_budget=0.1)
            services = AgentServices.build(client, cfg, store=Store.open(":memory:"), validate_dictionaries=False)
            p = await _create(services, 0.1)
            decision = await services.proposals.accept(p.proposal_id, USER)
            assert decision.code == "ok" and decision.execution.needs_submit
            assert services.store.proposals.get(p.proposal_id).needs_submit
            await services.proposals.tick()  # needs_submit retry
        req = create.calls.last.request
        assert req.headers["Idempotency-Key"] == p.proposal_id
        body = json.loads(req.content)
        assert body["return_target"] is True and body["initial_budget"] == 0.1
        assert submit.call_count == 2
    after = services.store.proposals.get(p.proposal_id)
    assert after.created_ad_id == 77 and not after.needs_submit
    assert services.store.ads.row(77)["proposal_id"] == p.proposal_id


# --- invariants (section 13) ---


def test_only_executor_calls_mutating_ads_methods():
    root = Path(__file__).resolve().parents[1] / "agent"
    mutating = re.compile(
        r"\.(create_ad|createAd|submit_ad_for_review|edit_ad|increase_ad_budget|decrease_ad_budget|edit_ad_status)\("
    )
    forbidden = re.compile(r"(delete_ad|deleteAd|increase_account_budget|decrease_account_budget|increaseAccountBudget|decreaseAccountBudget)")
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(root).as_posix()
        if mutating.search(text) and not rel.startswith("executor/"):
            offenders.append(rel)
        if forbidden.search(text):  # I7: never, not even in the executor
            offenders.append(rel + " (I7)")
    assert offenders == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["proposed", "rejected", "expired", "failed"])
async def test_no_create_ad_without_accepted_proposal(status):
    services, fake, _ = build_services(dry_run=False, first_live_max_budget=5)
    p = await _create(services)
    services.store.db.execute("UPDATE proposals SET status=?, decided_by=? WHERE proposal_id=?", (status, USER, p.proposal_id))
    result = await services.proposals.executor.execute(p.proposal_id)
    assert result.status == "refused"
    assert fake.mutating_calls == []


@pytest.mark.asyncio
async def test_double_callback_creates_one_ad():
    services, fake, _ = build_services(dry_run=False, first_live_max_budget=5)
    p = await _create(services)
    results = await asyncio.gather(*[services.proposals.accept(p.proposal_id, USER) for _ in range(5)])
    await services.proposals.accept(p.proposal_id, USER)
    assert [c[0] for c in fake.mutating_calls] == ["createAd"]
    codes = sorted(r.code for r in results)
    assert codes.count("ok") == 1 and all(c in {"ok", "already_created", "already_accepted"} for c in codes)


@pytest.mark.asyncio
async def test_executor_rerun_after_creation_is_noop():
    services, fake, _ = build_services(dry_run=False, first_live_max_budget=5)
    p = await _create(services)
    await services.proposals.accept(p.proposal_id, USER)
    again = await services.proposals.executor.execute(p.proposal_id)
    assert again.code == "already_created"
    assert len(fake.mutating_calls) == 1


@pytest.mark.asyncio
async def test_expired_proposal_cannot_be_accepted_and_releases_reserve():
    services, fake, clock = build_services(dry_run=False, first_live_max_budget=5)
    p = await _create(services)
    assert services.store.proposals.reserved_total() == 0.5
    clock.now += services.config.proposal_ttl_seconds
    decision = await services.proposals.accept(p.proposal_id, USER)
    assert decision.code == "expired"
    assert services.store.proposals.get(p.proposal_id).status == ProposalStatus.EXPIRED
    assert services.store.proposals.reserved_total() == 0
    assert fake.mutating_calls == []


@pytest.mark.asyncio
async def test_background_expiry_task_expires_due():
    services, _, clock = build_services()
    p = await _create(services)
    clock.now += services.config.proposal_ttl_seconds + 1
    await services.proposals.tick()
    assert services.store.proposals.get(p.proposal_id).status == ProposalStatus.EXPIRED


@pytest.mark.asyncio
@pytest.mark.parametrize("allow, user", [("200", 999), ("", 200), ("200", None)])
async def test_foreign_user_rejected(allow, user):
    services, fake, _ = build_services(dry_run=False, first_live_max_budget=5, allowed_user_ids_raw=allow)
    services.store.db.execute(
        "INSERT INTO proposals(proposal_id, created_ts, expires_ts, status, kind, payload_json, payload_hash, reserved_amount)"
        " VALUES('00000000-0000-0000-0000-000000000000', 0, 9999999999, 'proposed', 'create_ad', ?, 'x', 0)",
        (payload().model_dump_json(),),
    )
    pid = "00000000-0000-0000-0000-000000000000"
    assert (await services.proposals.accept(pid, user)).code == "forbidden"
    assert services.proposals.reject(pid, user).code == "forbidden"
    assert services.store.proposals.get(pid).status == ProposalStatus.PROPOSED
    assert fake.mutating_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["payload", "hash"])
async def test_tampered_payload_hash_rejected(tamper):
    services, fake, _ = build_services(dry_run=False, first_live_max_budget=5)
    p = await _create(services)
    if tamper == "payload":
        evil = json.loads(p.payload_json)
        evil["ad"]["promote_url"] = "https://t.me/evil"
        services.store.db.execute(
            "UPDATE proposals SET payload_json=? WHERE proposal_id=?", (json.dumps(evil), p.proposal_id)
        )
    else:
        services.store.db.execute("UPDATE proposals SET payload_hash='0'||payload_hash WHERE proposal_id=?", (p.proposal_id,))
    decision = await services.proposals.accept(p.proposal_id, USER)
    assert decision.code == "payload_hash_mismatch"
    assert services.store.proposals.get(p.proposal_id).status == ProposalStatus.FAILED
    assert services.store.proposals.reserved_total() == 0
    assert fake.mutating_calls == []


@pytest.mark.asyncio
async def test_balance_recheck_at_execution():
    services, fake, _ = build_services(dry_run=False, first_live_max_budget=5)
    p = await _create(services, 2.0)
    fake.account = fake.account.model_copy(update={"remaining_budget": 1.0})
    decision = await services.proposals.accept(p.proposal_id, USER)
    assert decision.code == "insufficient_balance"
    assert fake.mutating_calls == []
    assert services.store.proposals.reserved_total() == 0


@pytest.mark.asyncio
async def test_live_mode_guard_first_live_max_budget():
    services, fake, _ = build_services(dry_run=False)
    p = await _create(services)
    assert (await services.proposals.accept(p.proposal_id, USER)).code == "live_mode_needs_first_live_max_budget"
    services2, fake2, _ = build_services(dry_run=False, first_live_max_budget=0.2)
    p2 = await _create(services2, 0.5)
    assert (await services2.proposals.accept(p2.proposal_id, USER)).code == "over_first_live_max_budget"
    assert fake.mutating_calls == [] and fake2.mutating_calls == []


@pytest.mark.asyncio
async def test_create_refuses_over_free_balance_and_pending_limit():
    services, _, _ = build_services(FakeAds(remaining=1.0))
    assert (await services.proposals.create(payload(0.6))).proposal is not None
    over = await services.proposals.create(payload(0.6, text="other text"))
    assert over.proposal is None and over.errors[0].startswith("insufficient_free_balance")
    services2, _, _ = build_services(FakeAds(remaining=100.0), max_pending_proposals=2)
    for i in range(2):
        await _create(services2, 0.1, text=f"t{i}")
    assert (await services2.proposals.create(payload(0.1, text="t9"))).errors == ["max_pending_proposals"]


@pytest.mark.asyncio
async def test_reject_releases_reserve_and_records_reason():
    services, _, _ = build_services()
    p = await _create(services)
    assert services.proposals.reject(p.proposal_id, USER).code == "rejected"
    assert services.store.proposals.reserved_total() == 0
    assert services.proposals.set_reject_reason(p.proposal_id, USER, "geo").code == "reason_saved"
    after = services.store.proposals.get(p.proposal_id)
    assert after.reject_reason_code == "geo" and after.fingerprint
    assert (await services.proposals.accept(p.proposal_id, USER)).code == "already_rejected"


@pytest.mark.asyncio
async def test_api_error_marks_failed_and_transport_error_keeps_accepted():
    from ads_api.errors import AdsApiError, AdsTransportError

    services, fake, _ = build_services(dry_run=False, first_live_max_budget=5)
    p = await _create(services)

    async def boom(**kw):
        raise AdsApiError("CPM_TOO_LOW", method="createAd")

    fake.create_ad = boom
    assert (await services.proposals.accept(p.proposal_id, USER)).code == "CPM_TOO_LOW"
    assert services.store.proposals.get(p.proposal_id).status == ProposalStatus.FAILED

    services2, fake2, _ = build_services(dry_run=False, first_live_max_budget=5)
    p2 = await _create(services2)
    original = fake2.create_ad

    async def flaky(**kw):
        fake2.create_ad = original
        raise AdsTransportError("timeout", method="createAd")

    fake2.create_ad = flaky
    assert (await services2.proposals.accept(p2.proposal_id, USER)).code == "transport_error"
    assert services2.store.proposals.get(p2.proposal_id).status == ProposalStatus.ACCEPTED
    await services2.proposals.tick()  # idempotent retry with the same key
    assert services2.store.proposals.get(p2.proposal_id).status == ProposalStatus.CREATED


# --- property test: reserves never exceed free balance (I3) ---

ops = st.lists(
    st.tuples(st.sampled_from(["create", "accept", "reject", "expire", "drain"]), st.floats(min_value=0.01, max_value=4.0)),
    min_size=1,
    max_size=25,
)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(ops=ops, remaining=st.floats(min_value=0.5, max_value=10.0), dry=st.booleans())
def test_property_reserves_never_exceed_free_balance(ops, remaining, dry):
    async def run():
        fake = FakeAds(remaining=round(remaining, 2))
        services, _, clock = build_services(fake, dry_run=dry, first_live_max_budget=100, max_pending_proposals=50)
        repo = services.store.proposals
        for i, (op, amount) in enumerate(ops):
            amount = round(amount, 2)
            before = repo.reserved_total()
            if op == "create":
                await services.proposals.create(payload(amount, text=f"text {i}"))
            elif op == "accept" and repo.by_status("proposed"):
                await services.proposals.accept(repo.by_status("proposed")[0].proposal_id, USER)
            elif op == "reject" and repo.by_status("proposed"):
                services.proposals.reject(repo.by_status("proposed")[-1].proposal_id, USER)
            elif op == "expire":
                clock.now += services.config.proposal_ttl_seconds
                services.proposals.expire_due()
            elif op == "drain":
                # Balance shrinks outside the agent (spend): existing reserves may now exceed it,
                # but no NEW reservation may be added and execution must recheck.
                new = max(0.0, round(fake.account.remaining_budget - amount, 2))
                fake.account = fake.account.model_copy(update={"remaining_budget": new})
            after = repo.reserved_total()
            view = services.balance.view(fake.account)
            if after > before + 1e-9:
                # I3: a new reservation only fits inside the free balance.
                assert after <= view.free + view.reserved + 1e-6
            assert after <= before + 1e-9 or op == "create"
            created = [p for p in repo.by_status("created") if not p.dry_run]
            assert len(fake.mutating_calls) == len(created)
            for p in created:
                assert p.decided_by == USER

    asyncio.run(run())
