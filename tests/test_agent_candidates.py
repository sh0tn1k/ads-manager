"""Phase 4: candidate generators, dedup, scoring and the proposal cycle (explicit fixture values)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from ads_api.models import Ad
from agent.candidates.base import Candidate, changed_factors, input_target
from agent.candidates.dedup import candidate_spec, dedup, fingerprint_keys
from agent.candidates.grid import grid_candidates
from agent.candidates.history import history_candidates, rank_history
from agent.candidates.inputs import Creative, GeoPool, InputError, load_creatives, load_geo_pool, load_ledger
from agent.candidates.ledger import ledger_candidates
from agent.candidates.scoring import score_candidates, select
from agent.model.factors import factor_rows_from_store
from agent.proposals.models import ProposalStatus
from agent.validator import validate_payload
from tests.agent_helpers import PROMOTE_URL, FakeAds, api_ad, build_services

ROOT = Path(__file__).resolve().parents[1]
# Test fixture values (not production config).
INPUTS = dict(hurdle_cpa_ton=0.2, explore_share=0.2, max_loss_per_hypothesis=5.0, max_active_experiments=10, mc_samples=500)
GEO = "groups:\n  uk: [GB, IE]\n  dach: [DE, AT]\n  fr: [FR]\nlanguage_codes: [en]\ntopic_ids: [7, 9]\nbase_cpm: 0.12\n"
CREATIVES = "creatives:\n  - angle: news\n    text: Daily Premier League news\n  - angle: stats\n    text: Match stats and xG every weekend\n"
MODEL = dict(prior_ctr=0.0125, prior_ctr_strength=1000.0, prior_cvr=0.067, prior_cvr_strength=50.0, temper=0.6)


@pytest.fixture
def files(tmp_path):
    def write(geo: str | None = GEO, creatives: str | None = CREATIVES, ledger: str | None = None) -> dict:
        paths = {
            "geo_pool_path": str(tmp_path / "geo.yaml"),
            "creatives_path": str(tmp_path / "creatives.yaml"),
            "external_placements_path": str(tmp_path / "ext.csv"),
        }
        for key, content in (("geo_pool_path", geo), ("creatives_path", creatives), ("external_placements_path", ledger)):
            if content is not None:
                Path(paths[key]).write_text(content, encoding="utf-8")
        return paths
    return write


def _seed_history(services, *, status="active", ad_id=1, **extra):
    services.store.ads.upsert(Ad.model_validate(api_ad(ad_id, status=status, **extra)), 1)


# --- inputs ---

def test_example_templates_parse_and_are_not_real_data():
    pool = load_geo_pool(ROOT / "config/geo_pool.example.yaml")
    assert pool is not None and pool.groups and pool.base_cpm is None
    assert load_ledger(ROOT / "data/external_placements.example.csv") == []
    assert load_creatives(ROOT / "config/creatives.example.yaml")[0].angle == "example_angle"


def test_missing_files_are_none(tmp_path):
    assert load_geo_pool(tmp_path / "x.yaml") is None
    assert load_ledger(tmp_path / "x.csv") is None
    assert load_creatives(tmp_path / "c.yaml") is None


def test_invalid_files_raise_input_error(tmp_path):
    bad = tmp_path / "geo.yaml"
    bad.write_text("groups:\n  x: [GBR]\n")
    with pytest.raises(InputError):
        load_geo_pool(bad)
    csv = tmp_path / "ext.csv"
    csv.write_text("channel,price\n@a,1\n")
    with pytest.raises(InputError):
        load_ledger(csv)


def test_ledger_aggregates_per_channel(tmp_path):
    csv = tmp_path / "ext.csv"
    csv.write_text(
        "channel_username,date,price_usd,joins,views\n@Cheap,2026-01-01,10,10,1000\ncheap,2026-02-01,10,10,1000\n"
        "@pricey,2026-01-01,100,10,5000\n@nojoins,2026-01-01,50,0,100\n"
    )
    stats = {c.username: c for c in load_ledger(csv)}
    assert stats["cheap"].joins == 20 and stats["cheap"].cost_per_join_usd == 1.0
    cands = ledger_candidates(
        list(stats.values()), creative=Creative("news", "t"), base_cpm=0.1, promote_url=PROMOTE_URL, top_n=5, min_joins=1,
    )
    assert [c.target["channel_ids"] for c in cands] == [["@cheap"], ["@pricey"]]
    assert cands[0].cvr_shifts[("channels", "@cheap")] > 0 > cands[1].cvr_shifts[("channels", "@pricey")]
    assert all(c.placement == "channel_post" for c in cands)


# --- generators ---

def test_grid_is_one_factor_design_and_valid(files):
    services, _, _ = build_services(**INPUTS, **files())
    pool = load_geo_pool(services.config.geo_pool_path)
    creatives = load_creatives(services.config.creatives_path)
    cands = grid_candidates(pool, creatives=creatives, base_cpm=0.12, promote_url=PROMOTE_URL)
    # base + 2 other geo + 2 topics + 1 other angle
    assert len(cands) == 6
    assert all(len(c.changed) <= 1 for c in cands)
    from agent.proposals.models import Cost, ProposalKind, ProposalPayload
    for c in cands:
        payload = ProposalPayload(kind=ProposalKind.CREATE_AD, ad=candidate_spec(c, 0.7), cost=Cost(initial_budget=0.7, max_loss=0.7, reserved=0.7))
        assert validate_payload(payload, services.config) == []


def test_history_mutates_one_factor_of_best_ads():
    services, _, _ = build_services()
    _seed_history(services, ad_id=1)
    _seed_history(services, ad_id=2, clicks=0, actions=0)  # no clicks: not a "best" ad
    rows = factor_rows_from_store(services.store)
    assert [r.ad_id for r in rank_history(rows)] == [1]
    pool = GeoPool(groups={"uk": ["GB"], "de": ["DE"]}, topic_ids=[9])
    cands = history_candidates(rows, promote_url=PROMOTE_URL, creatives=[Creative("stats", "Stats")], geo_pool=pool, cpm_step=0.01, top_n=3)
    kinds = sorted(c.changed[0] for c in cands)
    assert kinds == ["angle", "cpm", "cpm", "geo", "topic"]
    base = rows[0].factors
    for c in cands:
        assert len(changed_factors(c.factors(0.05), base)) <= 1
        assert c.base_ad_id == 1


def test_input_target_converts_synced_shape():
    target = {"type": "users", "countries": [{"country_code": "GB"}], "languages": [{"language_code": "en"}],
              "topics": [{"topic_id": 7}]}
    assert input_target(target) == {"type": "users", "country_codes": ["GB"], "language_codes": ["en"], "topic_ids": [7]}
    assert input_target({"type": "search", "search_queries": ["epl"]}) == {"type": "search", "search_queries": ["epl"]}


# --- dedup / selection ---

def _cand(**kw) -> Candidate:
    base = dict(source="grid", title="t", text="Daily news", placement="video_banner",
                target={"type": "users", "country_codes": ["GB"]}, cpm=0.12, angle="news", promote_url=PROMOTE_URL,
                rationale_ru="r")
    base.update(kw)
    return Candidate(**base)


def test_dedup_matches_text_hash_and_label_and_within_batch():
    c = _cand()
    raw_keys = fingerprint_keys(candidate_spec(c), None, 0.05)
    fresh, dropped = dedup([c], raw_keys, width=0.05)
    assert fresh == [] and dropped == 1
    fresh, dropped = dedup([c, _cand(title="other")], set(), width=0.05)
    assert len(fresh) == 1 and dropped == 1
    fresh, _ = dedup([c, _cand(cpm=0.2)], set(), width=0.05)
    assert len(fresh) == 2


def test_select_threshold_budget_and_diversification():
    cands = [
        _cand(title="a"),
        _cand(title="b", target={"type": "users", "country_codes": ["DE"]}),
        _cand(title="c", target={"type": "users", "country_codes": ["DE"]}, angle="stats", text="Stats"),
    ]
    scored = score_candidates(cands, [], model_kwargs=MODEL, hurdle=0.2, stage_cost=0.7, volume_views=1e4,
                              samples=500, cpm_bucket_width=0.05, rng=np.random.default_rng(0))
    ref = {"users": scored[0].factors}
    chosen, below = select(scored, tau_min=0.0, budget_cap=10, cost=0.7, max_count=5, references=ref)
    assert {s.candidate.title for s in chosen} == {"a", "b"} and below == 0  # c changes geo and angle
    chosen, _ = select(scored, tau_min=0.0, budget_cap=0.7, cost=0.7, max_count=5, references=ref)
    assert len(chosen) == 1
    chosen, below = select(scored, tau_min=1.01, budget_cap=10, cost=0.7, max_count=5, references=ref)
    assert chosen == [] and below == 3


# --- cycle ---

@pytest.mark.asyncio
async def test_cycle_refuses_without_owner_inputs(files):
    services, fake, _ = build_services(**files())
    result = await services.cycle.run(200)
    assert result.created == [] and "HURDLE_CPA_TON" in result.reasons
    assert result.message_ru().startswith("Предложений нет: не задан порог HURDLE_CPA_TON")
    assert services.store.proposals.all() == [] and fake.mutating_calls == []


@pytest.mark.asyncio
async def test_cycle_forbidden_user(files):
    services, _, _ = build_services(**INPUTS, **files())
    assert (await services.cycle.run(999)).reasons == ["forbidden"]


@pytest.mark.asyncio
async def test_cycle_without_any_inputs_explains(files):
    services, _, _ = build_services(**INPUTS, **files(geo=None, creatives=None))
    result = await services.cycle.run(200)
    assert result.reasons == ["no_candidates"]
    assert {"no_history", "no_geo_pool", "no_ledger", "no_creatives"} <= set(result.notes)
    assert "нет кандидатов" in result.message_ru() and "geo_pool.yaml" in result.message_ru()


@pytest.mark.asyncio
async def test_cycle_without_base_cpm_says_so(files):
    services, _, _ = build_services(**INPUTS, **files(geo=GEO.replace("base_cpm: 0.12\n", "")))
    result = await services.cycle.run(200)
    assert result.reasons == ["no_candidates"] and "no_base_cpm" in result.notes


@pytest.mark.asyncio
async def test_cycle_creates_at_most_max_pending_and_valid_proposals(files):
    services, fake, _ = build_services(**INPUTS, **files())
    result = await services.cycle.run(200)
    assert 1 <= len(result.created) <= 3, result.message_ru()
    for p in result.created:
        payload = p.payload
        assert validate_payload(payload, services.config) == []
        assert payload.stage == 1 and payload.predicted.p_beats_hurdle >= services.config.tau_min
        assert payload.cost.initial_budget == 0.7 and payload.model_version == "posterior-v1"
        assert payload.ad.promote_url == PROMOTE_URL
    assert fake.mutating_calls == []
    again = await services.cycle.run(200)
    assert again.reasons == ["pending_full"] and "Предложений нет" in again.message_ru()


@pytest.mark.asyncio
async def test_cycle_respects_max_pending_setting(files):
    services, _, _ = build_services(**INPUTS, max_pending_proposals=1, **files())
    result = await services.cycle.run(200)
    assert len(result.created) == 1


@pytest.mark.asyncio
async def test_rejected_combinations_are_not_reproposed(files):
    services, _, _ = build_services(**INPUTS, **files())
    first = await services.cycle.run(200)
    fps = {p.fingerprint for p in first.created}
    for p in first.created:
        services.proposals.reject(p.proposal_id, 200)
    second = await services.cycle.run(200)
    assert not fps & {p.fingerprint for p in second.created}


@pytest.mark.asyncio
async def test_cycle_below_threshold(files):
    services, _, _ = build_services(**{**INPUTS, "hurdle_cpa_ton": 0.001}, **files())
    result = await services.cycle.run(200)
    assert result.reasons == ["below_threshold"] and "порог" in result.message_ru()


@pytest.mark.asyncio
async def test_cycle_no_budget(files):
    services, _, _ = build_services(fake=FakeAds(remaining=1.0), **INPUTS, **files())
    result = await services.cycle.run(200)
    assert result.reasons == ["no_budget"] and "нет свободного бюджета" in result.message_ru()


@pytest.mark.asyncio
async def test_cycle_bad_input_file(files):
    services, _, _ = build_services(**INPUTS, **files(geo="groups: [1, 2]\n"))
    result = await services.cycle.run(200)
    assert result.reasons == ["input_error"] and result.errors


@pytest.mark.asyncio
async def test_cycle_header_only_ledger_is_fine(files):
    header = (ROOT / "data/external_placements.example.csv").read_text()
    services, _, _ = build_services(**INPUTS, **files(ledger=header))
    result = await services.cycle.run(200)
    assert result.created and "no_ledger" not in result.notes


@pytest.mark.asyncio
async def test_cycle_from_history_never_reproposes_live_ad(files):
    services, _, _ = build_services(**INPUTS, **files(geo=None, creatives=None))
    _seed_history(services, ad_id=1)  # live
    result = await services.cycle.run(200)
    assert result.created, result.message_ru()
    live_fp = fingerprint_keys(candidate_spec(_cand(text="Premier League news every day", cpm=0.13,
                                                   target={"type": "users", "country_codes": ["GB"], "language_codes": ["en"], "topic_ids": [7]})), None, 0.05)
    assert not live_fp & {p.fingerprint for p in result.created}
    assert all(p.payload.hypothesis_id.startswith("history-") for p in result.created)


@pytest.mark.asyncio
async def test_control_added_for_finished_best_ad(files):
    services, _, clock = build_services(**INPUTS, **files())
    _seed_history(services, ad_id=1, status="stopped", remaining=0.0, created_date=clock.now - 86400)
    result = await services.cycle.run(200)
    sources = [p.payload.hypothesis_id.split("-")[0] for p in result.created]
    assert len(result.created) == 3 and sources.count("control") == 1
    control = next(p for p in result.created if p.payload.hypothesis_id.startswith("control-"))
    assert control.payload.ad.text == "Premier League news every day" and control.payload.ad.cpm == 0.13


@pytest.mark.asyncio
async def test_no_control_when_only_two_slots(files):
    services, _, clock = build_services(**INPUTS, max_pending_proposals=2, **files())
    _seed_history(services, ad_id=1, status="stopped", remaining=0.0, created_date=clock.now - 86400)
    result = await services.cycle.run(200)
    assert len(result.created) == 2
    assert not any(p.payload.hypothesis_id.startswith("control-") for p in result.created)


@pytest.mark.asyncio
async def test_expired_slots_free_up(files):
    services, _, clock = build_services(**INPUTS, **files())
    await services.cycle.run(200)
    clock.now += services.config.proposal_ttl_seconds + 1
    result = await services.cycle.run(200)
    assert result.created
    assert any(p.status == ProposalStatus.EXPIRED for p in services.store.proposals.all())
